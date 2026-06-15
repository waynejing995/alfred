from __future__ import annotations

from agentkit.stores.memory.base import MemoryProvider
from agentkit.stores.memory.types import MemoryContext, MemoryWrite


def memory_append(
    provider: MemoryProvider,
    *,
    ctx: MemoryContext,
    text: str,
    summary: str = "",
    entities: list[str] | None = None,
    source_session: str | None = None,
) -> dict[str, str]:
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                text=text,
                summary=summary,
                entities=entities or [],
                source_session=source_session,
            )
        ],
        ctx,
    )
    return {"status": "ok"}


def memory_replace(
    provider: MemoryProvider,
    *,
    ctx: MemoryContext,
    target: str,
    text: str,
    summary: str = "",
    entities: list[str] | None = None,
) -> dict[str, str]:
    provider.sync_turn(
        [
            MemoryWrite(
                op="replace",
                target=target,
                text=text,
                summary=summary,
                entities=entities or [],
            )
        ],
        ctx,
    )
    return {"status": "ok"}


def memory_search(
    provider: MemoryProvider,
    *,
    query: str,
    k: int = 10,
    project_id: str | None = None,
) -> dict:
    result = provider.search(query, k=k, project_id=project_id)
    return result.model_dump(mode="json")

