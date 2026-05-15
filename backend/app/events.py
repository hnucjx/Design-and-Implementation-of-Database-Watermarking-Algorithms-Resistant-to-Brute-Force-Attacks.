import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._listeners: set[asyncio.Queue[str]] = set()

    async def publish(self, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, default=str)
        for queue in list(self._listeners):
            queue.put_nowait(message)

    async def subscribe(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._listeners.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._listeners.discard(queue)
