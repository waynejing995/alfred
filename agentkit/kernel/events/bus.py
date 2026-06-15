from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any

from loguru import logger

from agentkit.kernel.events.base import Event, serialize
from agentkit.kernel.events.defs import SubscriberError

RESERVED = {"", "kernel", "alfred", "sys"}
Subscriber = Callable[[Event], Any]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = {}
        self._sinks: list[Any] = []

    def on(self, pattern: str, fn: Subscriber, *, owner_ns: str = "") -> Subscriber:
        self._subs.setdefault(pattern, []).append(fn)
        return fn

    def off(self, pattern: str, fn: Subscriber) -> None:
        subs = self._subs.get(pattern)
        if subs and fn in subs:
            subs.remove(fn)

    def register_emitter(self, event_cls: type[Event], owner_ns: str) -> None:
        namespace = event_cls.namespace
        if namespace in RESERVED and owner_ns not in ("", "kernel"):
            raise ValueError(f"{owner_ns!r} may not emit reserved event {event_cls.name!r}")
        if namespace and namespace != owner_ns:
            raise ValueError(f"{owner_ns!r} may not emit foreign event {event_cls.name!r}")

    async def emit(self, event: Event) -> None:
        frame = serialize(event)
        for sink in list(self._sinks):
            sink.put_nowait(frame)
        subscribers = self._match(event.name)
        if event.blockable:
            await self._emit_blocking(event, subscribers)
        else:
            await self._emit_isolated(event, subscribers)

    async def stream(self) -> AsyncIterator[dict]:
        import asyncio

        queue: asyncio.Queue = asyncio.Queue()
        self._sinks.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._sinks.remove(queue)

    def _match(self, name: str) -> list[Subscriber]:
        exact: list[Subscriber] = []
        glob: list[Subscriber] = []
        any_subs: list[Subscriber] = []
        for pattern, subscribers in self._subs.items():
            if pattern == name:
                exact.extend(subscribers)
            elif pattern == "*":
                any_subs.extend(subscribers)
            elif pattern.endswith(".*") and name.startswith(pattern[:-1]):
                glob.extend(subscribers)
        return [*exact, *glob, *any_subs]

    async def _emit_blocking(self, event: Event, subscribers: list[Subscriber]) -> None:
        for subscriber in subscribers:
            result = subscriber(event)
            if inspect.isawaitable(result):
                await result

    async def _emit_isolated(self, event: Event, subscribers: list[Subscriber]) -> None:
        import asyncio

        async def run(subscriber: Subscriber) -> None:
            try:
                result = subscriber(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                await self._report_subscriber_error(event, subscriber, exc)

        await asyncio.gather(*(run(subscriber) for subscriber in subscribers))

    async def _report_subscriber_error(
        self,
        event: Event,
        subscriber: Subscriber,
        exc: Exception,
    ) -> None:
        handler = getattr(subscriber, "__qualname__", repr(subscriber))
        logger.opt(exception=exc).error("subscriber {} failed on {}: {}", handler, event.name, exc)
        if event.name == SubscriberError.name:
            return
        error = SubscriberError(
            source_event=event.name,
            handler=handler,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        frame = serialize(error)
        for sink in list(self._sinks):
            sink.put_nowait(frame)
        await self._emit_isolated(error, self._match(error.name))

