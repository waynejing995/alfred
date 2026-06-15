from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderContextExceeded,
    ProviderError,
    ProviderRateLimit,
    ProviderTimeout,
    ProviderUnavailable,
)
from agentkit.kernel.providers.types import (
    ContentBlock,
    Message,
    ModelResponse,
    StreamDelta,
    ToolCall,
    ToolCallFragment,
    ToolDef,
    Usage,
)

try:
    import litellm
    from litellm import acompletion
except Exception:  # pragma: no cover - import failure is surfaced on construction/call.
    litellm = None
    acompletion = None


class LiteLLMProvider(ModelProvider):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str | None = None,
        http_headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.http_headers = http_headers or {}
        self.query_params = query_params or {}
        self.extra = extra or {}

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> ModelResponse:
        if acompletion is None:
            raise ProviderError("litellm is not importable")
        try:
            response = await acompletion(**self._call_kwargs(messages, tools, tool_choice, params))
        except Exception as exc:
            raise self._map_exc(exc) from exc
        return self._to_response(response)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> AsyncIterator[StreamDelta]:
        if acompletion is None:
            raise ProviderError("litellm is not importable")
        kwargs = self._call_kwargs(messages, tools, tool_choice, params)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        chunks: list[Any] = []
        try:
            response = await acompletion(**kwargs)
            async for chunk in response:
                chunks.append(chunk)
                choice = self._first_choice(chunk)
                delta = self._get(choice, "delta") if choice is not None else None
                usage = self._get(chunk, "usage")
                finish_reason = self._get(choice, "finish_reason") if choice is not None else None
                content = self._get(delta, "content") if delta is not None else None
                if content:
                    yield StreamDelta(text=content)
                for fragment in self._get(delta, "tool_calls", []) or []:
                    function = self._get(fragment, "function")
                    yield StreamDelta(
                        tool_call_fragment=ToolCallFragment(
                            index=self._get(fragment, "index", 0),
                            id=self._get(fragment, "id"),
                            name=self._get(function, "name") if function is not None else None,
                            arguments_delta=self._get(function, "arguments", "")
                            if function is not None
                            else "",
                        )
                    )
                if usage or finish_reason:
                    yield StreamDelta(
                        usage=self._to_usage(usage) if usage else None,
                        finish_reason=finish_reason,
                    )
            if litellm is not None and hasattr(litellm, "stream_chunk_builder"):
                full = litellm.stream_chunk_builder(chunks, messages=kwargs["messages"])
                yield StreamDelta(final_response=self._to_response(full))
        except Exception as exc:
            raise self._map_exc(exc) from exc

    def _call_kwargs(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None,
        tool_choice: str | None,
        params: dict[str, object],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_litellm_messages(messages),
            "api_key": self.api_key,
            **self.extra,
            **params,
        }
        if self.base_url:
            kwargs["api_base"] = self.base_url
        if self.http_headers:
            kwargs["extra_headers"] = self.http_headers
        if self.query_params:
            kwargs["extra_query"] = self.query_params
        tools_payload = self._to_litellm_tools(tools)
        if tools_payload:
            kwargs["tools"] = tools_payload
        if tool_choice:
            kwargs["tool_choice"] = self._to_tool_choice(tool_choice)
        return kwargs

    def _to_litellm_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for message in messages:
            data: dict[str, Any] = {"role": message.role}
            if isinstance(message.content, list):
                data["content"] = [self._block(block) for block in message.content]
            elif message.content is not None:
                data["content"] = message.content
            if message.tool_calls:
                data["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.raw_arguments or self._json_dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            if message.tool_call_id:
                data["tool_call_id"] = message.tool_call_id
            if message.name:
                data["name"] = message.name
            output.append(data)
        return output

    def _block(self, block: ContentBlock) -> dict[str, Any]:
        data: dict[str, Any] = {"type": "text", "text": block.text}
        if block.cache_control and self._is_anthropic_model():
            data["cache_control"] = block.cache_control
        return data

    @staticmethod
    def _to_litellm_tools(tools: list[ToolDef] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _to_tool_choice(tool_choice: str) -> str | dict[str, Any]:
        if tool_choice in {"auto", "none", "required"}:
            return tool_choice
        return {"type": "function", "function": {"name": tool_choice}}

    def _to_response(self, response: Any) -> ModelResponse:
        choice = self._first_choice(response)
        message = self._get(choice, "message")
        tool_calls: list[ToolCall] = []
        for raw_call in self._get(message, "tool_calls", []) or []:
            function = self._get(raw_call, "function")
            tool_calls.append(
                ToolCall.from_raw(
                    id=self._get(raw_call, "id", ""),
                    name=self._get(function, "name", ""),
                    raw_arguments=self._get(function, "arguments", "{}"),
                )
            )
        return ModelResponse(
            message=Message(
                role="assistant",
                content=self._get(message, "content"),
                tool_calls=tool_calls,
            ),
            usage=self._to_usage(self._get(response, "usage")),
            finish_reason=self._get(choice, "finish_reason"),
            model=self._get(response, "model", self.model),
            raw=self._dump(response),
        )

    @staticmethod
    def _to_usage(usage: Any) -> Usage:
        data = LiteLLMProvider._dump(usage) if usage is not None else {}
        details = data.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        return Usage(
            prompt_tokens=data.get("prompt_tokens", 0) or 0,
            completion_tokens=data.get("completion_tokens", 0) or 0,
            total_tokens=data.get("total_tokens", 0) or 0,
            cached_tokens=cached or 0,
            cache_creation_tokens=data.get("cache_creation_input_tokens", 0) or 0,
        )

    def _is_anthropic_model(self) -> bool:
        model = self.model.lower()
        return model.startswith("anthropic/") or model.startswith("claude")

    def _map_exc(self, exc: Exception) -> ProviderError:
        name = type(exc).__name__
        mapping = {
            "AuthenticationError": ProviderAuthError,
            "PermissionDeniedError": ProviderAuthError,
            "RateLimitError": ProviderRateLimit,
            "Timeout": ProviderTimeout,
            "APITimeoutError": ProviderTimeout,
            "APIConnectionError": ProviderUnavailable,
            "ServiceUnavailableError": ProviderUnavailable,
            "InternalServerError": ProviderUnavailable,
            "ContextWindowExceededError": ProviderContextExceeded,
            "BadRequestError": ProviderBadRequest,
            "NotFoundError": ProviderBadRequest,
        }
        return mapping.get(name, ProviderError)(str(exc))

    @staticmethod
    def _json_dumps(value: dict[str, Any]) -> str:
        import json

        return json.dumps(value)

    @staticmethod
    def _first_choice(value: Any) -> Any:
        choices = LiteLLMProvider._get(value, "choices", []) or []
        return choices[0] if choices else None

    @staticmethod
    def _get(value: Any, key: str, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @staticmethod
    def _dump(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "dict"):
            return value.dict()
        try:
            return dict(value)
        except Exception:
            return {
                key: val
                for key, val in vars(value).items()
                if not key.startswith("_")
            }
