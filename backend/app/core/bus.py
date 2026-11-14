"""In-process event bus: runtime publishes AgentEventPayload, WebSocket clients subscribe."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from typing import Iterator

from backend.app.core.events import AgentEventPayload


class EventBus:
    def __init__(self, queue_size: int = 1000, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[AgentEventPayload]] = set()
        self._queue_size = queue_size
        self._recent: deque[AgentEventPayload] = deque(maxlen=history)

    @property
    def recent(self) -> list[AgentEventPayload]:
        """Last events, replayed to a client that (re)connects."""
        return list(self._recent)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[AgentEventPayload]]:
        queue: asyncio.Queue[AgentEventPayload] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def publish(self, event: AgentEventPayload) -> None:
        self._recent.append(event)
        for queue in list(self._subscribers):
            if queue.full():
                # A slow client loses its oldest event instead of blocking the agents.
                queue.get_nowait()
            queue.put_nowait(event)
