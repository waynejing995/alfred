from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from agentkit.kernel.providers.types import Message, ModelResponse, StreamDelta, ToolDef


class ModelProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> AsyncIterator[StreamDelta]:
        raise NotImplementedError

