from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any

from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.types import Message, ModelResponse, StreamDelta, ToolDef, Usage


class MockProvider(ModelProvider):
    """Deterministic provider for unit tests and local CLI smoke runs."""

    def __init__(
        self,
        responses: Iterable[str | Message | ModelResponse] | None = None,
        *,
        model: str = "mock",
    ) -> None:
        self._responses = list(responses or [])
        self.model = model
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> ModelResponse:
        self.calls.append(messages)
        if self._responses:
            response = self._responses.pop(0)
        else:
            response = self._default_response(messages)
        return self._coerce(response)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> AsyncIterator[StreamDelta]:
        response = await self.complete(messages, tools=tools, tool_choice=tool_choice, **params)
        content = response.message.content if isinstance(response.message.content, str) else ""
        if content:
            yield StreamDelta(text=content)
        yield StreamDelta(
            usage=response.usage,
            finish_reason=response.finish_reason,
            final_response=response,
        )

    def _default_response(self, messages: list[Message]) -> str:
        last = next((m for m in reversed(messages) if m.role == "user"), None)
        text = last.content if last and isinstance(last.content, str) else ""
        return f"mock: {text}"

    def _coerce(self, response: str | Message | ModelResponse) -> ModelResponse:
        if isinstance(response, ModelResponse):
            return response
        if isinstance(response, Message):
            message = response
        else:
            message = Message(role="assistant", content=response)
        content = message.content if isinstance(message.content, str) else ""
        prompt_tokens = sum(len(str(call.content or "").split()) for call in self.calls[-1])
        completion_tokens = len(content.split())
        return ModelResponse(
            message=message,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            finish_reason="tool_calls" if message.tool_calls else "stop",
            model=self.model,
            raw={"mock": True},
        )

    @classmethod
    def tool_call(
        cls,
        *,
        name: str,
        arguments: dict[str, Any],
        call_id: str = "call_1",
        final: str = "done",
    ) -> "MockProvider":
        from agentkit.kernel.providers.types import ToolCall

        return cls(
            [
                Message(
                    role="assistant",
                    content=None,
                    tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                ),
                final,
            ]
        )

