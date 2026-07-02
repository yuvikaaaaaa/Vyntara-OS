"""
Intelligence Operating System — Redis Infrastructure
====================================================
Provides all Redis connectivity and primitive operations:

``RedisClientFactory``
    Creates and validates the async Redis client from settings.

``RedisCache``
    Generic async cache with typed get/set/delete/exists operations,
    serialisation via ``orjson``, and per-key TTL management.

``RedisPubSub``
    Pub/Sub channel manager for inter-service event broadcasting
    (used by the WebSocket notification service).

``RedisLock``
    Distributed advisory lock using Redis SET NX EX pattern.

``build_key(*parts)``
    Namespace-aware key builder that enforces the ``ios:<env>:<ns>:<id>``
    key convention from the SDD.

All functions are async.  The module never stores state — callers supply
the ``redis.asyncio.Redis`` client (obtained from ``app.state.redis`` via
FastAPI dependency injection).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import orjson
import redis.asyncio as aioredis
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from app.core.config import get_settings
from app.core.exceptions import (
    WorkingMemoryError,
)
from app.core.logging import get_logger
from app.core.telemetry import create_async_span

logger = get_logger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


def build_key(*parts: str) -> str:
    """
    Build a namespaced Redis key from path segments.

    Enforces the IOS key convention::

        ios:<environment>:<segment1>:<segment2>:...

    Args:
        *parts: Key path segments (e.g. ``"session", "working_memory", session_id``).

    Returns:
        Colon-delimited Redis key string.

    Example::

        build_key("session", "working_memory", "abc-123")
        # → "ios:production:session:working_memory:abc-123"
    """
    settings = get_settings()
    env = settings.environment
    sanitised = [p.replace(":", "_").replace(" ", "_") for p in parts if p]
    return f"ios:{env}:" + ":".join(sanitised)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def create_redis_client(settings: Any = None) -> aioredis.Redis:
    """
    Create a new async Redis client from application settings.

    The client is *not* a singleton — callers are expected to use the
    process-scoped instance stored in ``app.state.redis`` (set during
    startup in ``events.py``).  This factory is provided for testing and
    management scripts that need an isolated client.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``redis.asyncio.Redis`` instance.
    """
    cfg = settings or get_settings()
    client = aioredis.from_url(
        cfg.redis.url,
        max_connections=cfg.redis.max_connections,
        socket_timeout=cfg.redis.socket_timeout,
        socket_connect_timeout=cfg.redis.socket_connect_timeout,
        decode_responses=True,
        health_check_interval=30,
    )
    logger.info(
        "redis_client_created",
        host=cfg.redis.host,
        port=cfg.redis.port,
        max_connections=cfg.redis.max_connections,
    )
    return client


async def ping_redis(client: aioredis.Redis) -> bool:
    """
    Send a PING command to verify Redis connectivity.

    Args:
        client: Async Redis client.

    Returns:
        ``True`` if Redis responds, ``False`` otherwise.
    """
    try:
        return await client.ping()
    except (RedisConnectionError, RedisTimeoutError, RedisError) as exc:
        logger.warning("redis_ping_failed", exc=str(exc))
        return False


# ---------------------------------------------------------------------------
# Generic typed cache
# ---------------------------------------------------------------------------


class RedisCache:
    """
    Generic async cache backed by Redis.

    Serialises values to JSON via ``orjson`` (faster than stdlib ``json``)
    and applies per-key TTL expiry.  Designed for:
    - LLM response caching (semantic cache)
    - Embedding vector caching
    - Session state snapshots

    Args:
        client: Async Redis client (from ``app.state.redis``).
        namespace: Key namespace segment (e.g. ``"cache:llm"``).
        default_ttl: Default TTL in seconds for keys that don't specify one.
    """

    def __init__(
        self,
        client: aioredis.Redis,
        namespace: str,
        default_ttl: int = 3_600,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._default_ttl = default_ttl

    def _make_key(self, key: str) -> str:
        return build_key(self._namespace, key)

    async def get(self, key: str) -> Any | None:
        """
        Retrieve a cached value by key.

        Args:
            key: Cache key (un-namespaced; namespace is prepended automatically).

        Returns:
            Deserialised Python object, or ``None`` if not found / expired.
        """
        async with create_async_span(
            "redis.cache.get",
            attributes={"cache.namespace": self._namespace},
        ):
            try:
                raw = await self._client.get(self._make_key(key))
                if raw is None:
                    return None
                return orjson.loads(raw)
            except (RedisError, orjson.JSONDecodeError) as exc:
                logger.warning("redis_cache_get_error", key=key, exc=str(exc))
                return None

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: int | None = None,
    ) -> bool:
        """
        Store a value in the cache.

        Args:
            key: Cache key.
            value: JSON-serialisable Python object.
            ttl: TTL in seconds.  Uses ``default_ttl`` if ``None``.

        Returns:
            ``True`` on success, ``False`` on error.
        """
        async with create_async_span(
            "redis.cache.set",
            attributes={"cache.namespace": self._namespace},
        ):
            effective_ttl = ttl if ttl is not None else self._default_ttl
            try:
                serialised = orjson.dumps(value)
                await self._client.setex(
                    self._make_key(key),
                    effective_ttl,
                    serialised,
                )
                return True
            except (RedisError, TypeError) as exc:
                logger.warning("redis_cache_set_error", key=key, exc=str(exc))
                return False

    async def delete(self, key: str) -> int:
        """
        Delete a cache entry.

        Args:
            key: Cache key.

        Returns:
            Number of keys deleted (0 or 1).
        """
        try:
            return await self._client.delete(self._make_key(key))
        except RedisError as exc:
            logger.warning("redis_cache_delete_error", key=key, exc=str(exc))
            return 0

    async def exists(self, key: str) -> bool:
        """
        Check whether a key exists in the cache.

        Args:
            key: Cache key.

        Returns:
            ``True`` if the key exists and has not expired.
        """
        try:
            return bool(await self._client.exists(self._make_key(key)))
        except RedisError:
            return False

    async def ttl(self, key: str) -> int:
        """
        Return the remaining TTL for a key in seconds.

        Args:
            key: Cache key.

        Returns:
            Remaining TTL in seconds; -1 if key has no TTL; -2 if key does not exist.
        """
        try:
            return await self._client.ttl(self._make_key(key))
        except RedisError:
            return -2

    async def keys_with_prefix(self, prefix: str) -> list[str]:
        """
        Return all keys under a prefix within this cache's namespace.

        **Use sparingly in production** — SCAN is O(N) over the keyspace.

        Args:
            prefix: Key prefix (un-namespaced).

        Returns:
            List of matching key strings (without namespace prefix).
        """
        pattern = self._make_key(prefix) + "*"
        ns_prefix = build_key(self._namespace, "")
        keys: list[str] = []
        async for key in self._client.scan_iter(pattern, count=100):
            keys.append(key.removeprefix(ns_prefix))
        return keys

    async def flush_namespace(self) -> int:
        """
        Delete all keys within this cache's namespace.

        **Testing and maintenance only.**

        Returns:
            Number of keys deleted.
        """
        pattern = build_key(self._namespace, "") + "*"
        count = 0
        pipeline = self._client.pipeline(transaction=False)
        async for key in self._client.scan_iter(pattern, count=200):
            pipeline.delete(key)
            count += 1
        if count:
            await pipeline.execute()
        logger.info("redis_cache_flushed", namespace=self._namespace, deleted=count)
        return count


# ---------------------------------------------------------------------------
# Hash store (for working memory / session state)
# ---------------------------------------------------------------------------


class RedisHashStore:
    """
    Redis Hash-based key-value store for structured session state.

    Stores multiple fields under a single Redis Hash key, which is more
    memory-efficient than separate string keys for related fields.

    Used for:
    - Agent working memory (one hash per session)
    - Session state snapshots

    Args:
        client: Async Redis client.
        hash_key: The Redis Hash key (fully namespaced).
        ttl: TTL in seconds for the entire hash (refreshed on every write).
    """

    def __init__(
        self,
        client: aioredis.Redis,
        hash_key: str,
        ttl: int = 86_400,
    ) -> None:
        self._client = client
        self._hash_key = hash_key
        self._ttl = ttl

    async def hset(self, field: str, value: Any) -> None:
        """
        Set a field in the hash and refresh the hash TTL.

        Args:
            field: Hash field name.
            value: JSON-serialisable value.
        """
        try:
            serialised = orjson.dumps(value).decode()
            pipe = self._client.pipeline()
            pipe.hset(self._hash_key, field, serialised)
            pipe.expire(self._hash_key, self._ttl)
            await pipe.execute()
        except RedisError as exc:
            raise WorkingMemoryError(
                f"Failed to write working memory field '{field}': {exc}",
                details={"hash_key": self._hash_key, "field": field},
            ) from exc

    async def hget(self, field: str) -> Any | None:
        """
        Get a field from the hash.

        Args:
            field: Hash field name.

        Returns:
            Deserialised value or ``None`` if not present.
        """
        try:
            raw = await self._client.hget(self._hash_key, field)
            if raw is None:
                return None
            return orjson.loads(raw)
        except (RedisError, orjson.JSONDecodeError) as exc:
            logger.warning(
                "redis_hash_get_error",
                hash_key=self._hash_key,
                field=field,
                exc=str(exc),
            )
            return None

    async def hgetall(self) -> dict[str, Any]:
        """
        Return all fields in the hash as a dictionary.

        Returns:
            Dict mapping field names to deserialised values.
        """
        try:
            raw = await self._client.hgetall(self._hash_key)
            return {k: orjson.loads(v) for k, v in raw.items()}
        except (RedisError, orjson.JSONDecodeError) as exc:
            logger.warning(
                "redis_hash_getall_error",
                hash_key=self._hash_key,
                exc=str(exc),
            )
            return {}

    async def hdel(self, *fields: str) -> int:
        """
        Delete one or more fields from the hash.

        Args:
            *fields: Field names to delete.

        Returns:
            Number of fields deleted.
        """
        try:
            return await self._client.hdel(self._hash_key, *fields)
        except RedisError as exc:
            logger.warning(
                "redis_hash_del_error",
                hash_key=self._hash_key,
                exc=str(exc),
            )
            return 0

    async def hexists(self, field: str) -> bool:
        """Check whether a field exists in the hash."""
        try:
            return bool(await self._client.hexists(self._hash_key, field))
        except RedisError:
            return False

    async def delete(self) -> None:
        """Delete the entire hash."""
        try:
            await self._client.delete(self._hash_key)
        except RedisError as exc:
            logger.warning(
                "redis_hash_delete_error",
                hash_key=self._hash_key,
                exc=str(exc),
            )

    async def refresh_ttl(self) -> None:
        """Reset the hash TTL to ``self._ttl`` seconds from now."""
        try:
            await self._client.expire(self._hash_key, self._ttl)
        except RedisError:
            pass


# ---------------------------------------------------------------------------
# Pub/Sub manager
# ---------------------------------------------------------------------------


class RedisPubSub:
    """
    Pub/Sub channel manager for real-time event broadcasting.

    Used by the ``NotificationService`` to fan out WebSocket events to all
    connected clients subscribed to a task or session channel.

    Args:
        client: Async Redis client.
    """

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    def _channel(self, *parts: str) -> str:
        return build_key("pubsub", *parts)

    async def publish(
        self,
        channel_parts: list[str],
        message: dict[str, Any],
    ) -> int:
        """
        Publish a message to a channel.

        Args:
            channel_parts: Channel path segments (e.g. ``["task", task_id]``).
            message: JSON-serialisable event payload.

        Returns:
            Number of subscribers that received the message.
        """
        channel = self._channel(*channel_parts)
        try:
            payload = orjson.dumps(message).decode()
            receivers = await self._client.publish(channel, payload)
            logger.debug(
                "pubsub_published",
                channel=channel,
                receivers=receivers,
            )
            return receivers
        except RedisError as exc:
            logger.error(
                "pubsub_publish_error",
                channel=channel,
                exc=str(exc),
            )
            return 0

    @contextlib.asynccontextmanager
    async def subscribe(
        self,
        *channel_parts_list: list[str],
    ) -> AsyncIterator[PubSub]:
        """
        Context manager that subscribes to one or more channels.

        Automatically unsubscribes and closes the Pub/Sub connection on exit.

        Args:
            *channel_parts_list: Each argument is a list of channel path
                                  segments to subscribe to.

        Yields:
            Active ``PubSub`` object for iterating messages.

        Example::

            async with pubsub.subscribe(["task", task_id]) as ps:
                async for raw in ps.listen():
                    if raw["type"] == "message":
                        event = orjson.loads(raw["data"])
                        await handle(event)
        """
        channels = [self._channel(*parts) for parts in channel_parts_list]
        ps: PubSub = self._client.pubsub()
        try:
            await ps.subscribe(*channels)
            logger.debug("pubsub_subscribed", channels=channels)
            yield ps
        finally:
            with contextlib.suppress(Exception):
                await ps.unsubscribe(*channels)
            with contextlib.suppress(Exception):
                await ps.aclose()
            logger.debug("pubsub_unsubscribed", channels=channels)


# ---------------------------------------------------------------------------
# Distributed lock
# ---------------------------------------------------------------------------


class RedisLock:
    """
    Distributed advisory lock using Redis SET NX EX.

    Prevents duplicate agent executions for the same task step across
    multiple workers.

    Args:
        client: Async Redis client.
        lock_name: Unique lock identifier (will be namespaced automatically).
        ttl: Lock expiry in seconds (default 300 s = 5 minutes).
    """

    def __init__(
        self,
        client: aioredis.Redis,
        lock_name: str,
        ttl: int = 300,
    ) -> None:
        self._client = client
        self._key = build_key("lock", lock_name)
        self._ttl = ttl
        self._token: str | None = None

    async def acquire(self, *, timeout: float = 10.0) -> bool:
        """
        Attempt to acquire the lock, waiting up to ``timeout`` seconds.

        Args:
            timeout: Maximum seconds to wait for the lock.

        Returns:
            ``True`` if the lock was acquired, ``False`` if timed out.
        """
        token = str(uuid.uuid4())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            acquired = await self._client.set(
                self._key, token, nx=True, ex=self._ttl
            )
            if acquired:
                self._token = token
                logger.debug("redis_lock_acquired", key=self._key)
                return True
            await asyncio.sleep(0.05)
        logger.warning("redis_lock_timeout", key=self._key, timeout=timeout)
        return False

    async def release(self) -> bool:
        """
        Release the lock.  Only releases if this instance holds the lock.

        Uses a Lua script for atomic check-and-delete to prevent releasing
        a lock held by another process.

        Returns:
            ``True`` if released, ``False`` if the lock was not held by us.
        """
        if self._token is None:
            return False
        lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
        """
        result = await self._client.eval(lua_script, 1, self._key, self._token)
        released = bool(result)
        if released:
            self._token = None
            logger.debug("redis_lock_released", key=self._key)
        else:
            logger.warning("redis_lock_release_failed", key=self._key)
        return released

    @contextlib.asynccontextmanager
    async def __aenter__(self) -> "RedisLock":
        acquired = await self.acquire()
        if not acquired:
            from app.core.exceptions import AgentError
            raise AgentError(
                f"Could not acquire distributed lock '{self._key}' within timeout.",
                code="LOCK_ACQUISITION_TIMEOUT",
            )
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.release()


# ---------------------------------------------------------------------------
# Rate limiter (sliding window)
# ---------------------------------------------------------------------------


async def check_rate_limit(
    client: aioredis.Redis,
    identifier: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int, int]:
    """
    Sliding window rate limiter using Redis sorted sets.

    Implements the sliding window algorithm:
    1. Remove all entries older than ``window_seconds``.
    2. Count remaining entries.
    3. If count < ``max_requests``, add the current timestamp and allow.
    4. Otherwise deny.

    Args:
        client: Async Redis client.
        identifier: Rate limit key (e.g. user UUID or IP address).
        max_requests: Maximum allowed requests per window.
        window_seconds: Window duration in seconds.

    Returns:
        3-tuple of ``(allowed: bool, current_count: int, remaining: int)``.
    """
    key = build_key("rate_limit", identifier)
    now = time.time()
    window_start = now - window_seconds

    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    pipe.zadd(key, {str(uuid.uuid4()): now})
    pipe.expire(key, window_seconds + 1)
    results = await pipe.execute()

    current_count: int = int(results[1])
    allowed = current_count < max_requests
    remaining = max(0, max_requests - current_count - 1)

    if not allowed:
        # Remove the entry we just added — we're denying this request
        await client.zremrangebyscore(key, now, now + 0.001)

    return allowed, current_count, remaining


# ---------------------------------------------------------------------------
# Stream (append-only event log per task)
# ---------------------------------------------------------------------------


async def stream_append(
    client: aioredis.Redis,
    stream_key: str,
    fields: dict[str, Any],
    *,
    maxlen: int = 10_000,
) -> str:
    """
    Append a message to a Redis Stream.

    Used by the streaming notification service to persist WebSocket events
    for reconnection replay.

    Args:
        client: Async Redis client.
        stream_key: Full (namespaced) stream key.
        fields: Message fields (values serialised to strings).
        maxlen: Maximum stream length (older entries are trimmed).

    Returns:
        Redis stream entry ID string.
    """
    serialised = {
        k: orjson.dumps(v).decode() if not isinstance(v, str) else v
        for k, v in fields.items()
    }
    entry_id: str = await client.xadd(
        stream_key,
        serialised,
        maxlen=maxlen,
        approximate=True,
    )
    return entry_id


async def stream_read_from(
    client: aioredis.Redis,
    stream_key: str,
    last_id: str = "0-0",
    count: int = 100,
) -> list[tuple[str, dict[str, Any]]]:
    """
    Read entries from a Redis Stream starting after ``last_id``.

    Used for WebSocket reconnection — clients supply their last seen
    event ID and receive all missed events.

    Args:
        client: Async Redis client.
        stream_key: Full (namespaced) stream key.
        last_id: Entry ID to read from (exclusive).  ``"0-0"`` reads all.
        count: Maximum number of entries to return.

    Returns:
        List of ``(entry_id, fields_dict)`` tuples.
    """
    try:
        raw = await client.xread(
            streams={stream_key: last_id},
            count=count,
        )
        if not raw:
            return []
        # raw = [(stream_name, [(id, fields), ...])]
        entries: list[tuple[str, dict[str, Any]]] = []
        for _stream_name, messages in raw:
            for entry_id, raw_fields in messages:
                decoded = {
                    k: orjson.loads(v) if _is_json(v) else v
                    for k, v in raw_fields.items()
                }
                entries.append((entry_id, decoded))
        return entries
    except RedisError as exc:
        logger.warning("redis_stream_read_error", stream_key=stream_key, exc=str(exc))
        return []


def _is_json(value: str) -> bool:
    """Quick check whether a string looks like JSON."""
    return isinstance(value, str) and (
        value.startswith("{")
        or value.startswith("[")
        or value.startswith('"')
        or value in ("true", "false", "null")
        or (value.lstrip("-").replace(".", "", 1).isdigit())
    )