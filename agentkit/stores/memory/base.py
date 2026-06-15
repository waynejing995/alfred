from __future__ import annotations

from abc import ABC, abstractmethod

from agentkit.stores.memory.types import MemoryContext, MemoryWrite, RetrievedMemory


class MemoryProvider(ABC):
    @abstractmethod
    def prefetch(self, ctx: MemoryContext) -> RetrievedMemory:
        raise NotImplementedError

    @abstractmethod
    def sync_turn(self, writes: list[MemoryWrite], ctx: MemoryContext) -> None:
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> None:
        raise NotImplementedError

    def search(self, query: str, k: int = 10, *, project_id: str | None = None) -> RetrievedMemory:
        raise NotImplementedError

