"""IOS Agents — Agent Communication (Message Bus)."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable
from uuid import uuid4

from app.agents.base import BaseAgentComponent
from app.agents.exceptions import AgentCommunicationError, AgentTimeoutError
from app.agents.interfaces import IMessageBus
from app.agents.types import AgentMessage, MessageType

Handler = Callable[[AgentMessage], Awaitable[None]]


class AgentCommunication(BaseAgentComponent, IMessageBus):
    """
    In-process publish/subscribe message bus for inter-agent communication.

    Framework-independent: implemented purely with asyncio primitives
    (no Redis, no external broker) — suitable for single-process agent
    coordination within one task execution. A future distributed variant
    could implement the same IMessageBus contract backed by Redis Pub/Sub
    without any change to callers.

    Supports:
    - publish(): fire-and-forget delivery to all subscribers of a topic
    - subscribe(): register an async handler for a topic
    - broadcast(): publish to a reserved wildcard topic all agents may
      optionally subscribe to
    - request()/respond(): correlation-id-based request/response pattern
      built on top of publish/subscribe, with timeout support
    """

    _BROADCAST_TOPIC = "__broadcast__"

    def __init__(self) -> None:
        super().__init__()
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._pending_responses: dict[str, asyncio.Future[AgentMessage]] = {}

    async def publish(self, message: AgentMessage) -> None:
        async with self._span("publish", topic=message.topic):
            handlers = list(self._subscribers.get(message.topic, []))

            # Deliver to any pending request/response waiter first
            if message.type == MessageType.RESPONSE and message.correlation_id:
                future = self._pending_responses.pop(message.correlation_id, None)
                if future and not future.done():
                    future.set_result(message)

            if not handlers:
                self._log.debug("publish_no_subscribers", topic=message.topic)
                return

            results = await asyncio.gather(
                *(self._safe_invoke(h, message) for h in handlers),
                return_exceptions=True,
            )
            failures = [r for r in results if isinstance(r, Exception)]
            if failures:
                self._log.warning(
                    "publish_handler_failures",
                    topic=message.topic,
                    failures=len(failures),
                )

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)
        self._log.debug("subscribed", topic=topic)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def request(
        self, message: AgentMessage, *, timeout_seconds: float = 30.0
    ) -> AgentMessage:
        """
        Send a REQUEST message and await a correlated RESPONSE.

        Raises:
            AgentCommunicationError: message.type is not REQUEST, or no
                                      correlation_id was supplied and one
                                      could not be auto-assigned.
            AgentTimeoutError: No response received within timeout_seconds.
        """
        async with self._span("request", topic=message.topic):
            if message.type != MessageType.REQUEST:
                raise AgentCommunicationError(
                    "request() requires a message with type=MessageType.REQUEST.",
                )
            correlation_id = message.correlation_id or str(uuid4())
            message.correlation_id = correlation_id

            future: asyncio.Future[AgentMessage] = asyncio.get_event_loop().create_future()
            self._pending_responses[correlation_id] = future

            await self.publish(message)

            try:
                return await asyncio.wait_for(future, timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                self._pending_responses.pop(correlation_id, None)
                raise AgentTimeoutError(
                    f"No response received for request on topic '{message.topic}' "
                    f"within {timeout_seconds}s.",
                    details={"topic": message.topic, "correlation_id": correlation_id},
                ) from exc

    async def respond(
        self, original: AgentMessage, payload: dict, *, sender: str
    ) -> None:
        """Convenience helper: publish a RESPONSE correlated to a REQUEST."""
        response = AgentMessage(
            type=MessageType.RESPONSE,
            topic=original.topic,
            sender=sender,
            recipient=original.sender,
            payload=payload,
            correlation_id=original.correlation_id,
        )
        await self.publish(response)

    async def broadcast(self, message: AgentMessage) -> None:
        async with self._span("broadcast"):
            message.type = MessageType.BROADCAST
            message.topic = self._BROADCAST_TOPIC
            await self.publish(message)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, []))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _safe_invoke(self, handler: Handler, message: AgentMessage) -> None:
        try:
            await handler(message)
        except Exception as exc:
            self._log.warning(
                "message_handler_error", topic=message.topic, exc=str(exc)
            )
            raise