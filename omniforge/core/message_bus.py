"""In-process publish/subscribe bus with a bounded history ring.

Agents never talk to each other directly; they publish structured events and
read the shared history. That keeps task state in one place instead of spread
across private agent conversations.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable

from .events import Event


class Subscription:
    def __init__(self, bus: MessageBus, run_id: str | None, types: Iterable[str] | None):
        self._bus = bus
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)
        self.run_id = run_id
        self.types = set(types) if types else None

    def matches(self, event: Event) -> bool:
        if self.run_id and event.run_id != self.run_id:
            return False
        if self.types and event.type not in self.types:
            return False
        return True

    def offer(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer: drop rather than block the bus

    async def stream(self) -> AsyncIterator[Event]:
        try:
            while True:
                yield await self._queue.get()
        finally:
            self._bus.unsubscribe(self)

    async def next(self, timeout: float = 5.0) -> Event | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def close(self) -> None:
        self._bus.unsubscribe(self)


class MessageBus:
    def __init__(self, history: int = 2000) -> None:
        self._subs: list[Subscription] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[Event], None]] = []

    async def publish(self, event: Event) -> Event:
        async with self._lock:
            self._history.append(event)
            subs = list(self._subs)
        for sub in subs:
            if sub.matches(event):
                sub.offer(event)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - a bad listener must not break a run
                pass
        return event

    def add_listener(self, fn: Callable[[Event], None]) -> None:
        """Synchronous sink for every event (persistence, metrics)."""
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[Event], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    async def emit(self, type: str, **kw) -> Event:
        return await self.publish(Event(type=type, **kw))

    def subscribe(self, run_id: str | None = None, types: Iterable[str] | None = None) -> Subscription:
        sub = Subscription(self, run_id, types)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    def history(self, run_id: str | None = None, limit: int = 200) -> list[dict]:
        items = [e for e in self._history if run_id is None or e.run_id == run_id]
        return [e.to_dict() for e in items[-limit:]]

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
