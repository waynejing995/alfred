from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.providers.errors import ProviderBadRequest

Role = Literal["system", "user", "assistant", "tool"]


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["text"] = "text"
    text: str
    cache_control: dict[str, Any] | None = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str = ""

    @classmethod
    def from_raw(cls, *, id: str, name: str, raw_arguments: str | None) -> "ToolCall":
        raw = raw_arguments or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderBadRequest(f"tool call args not JSON: {raw!r}") from exc
        if not isinstance(parsed, dict):
            raise ProviderBadRequest(f"tool call args must decode to an object: {raw!r}")
        return cls(id=id, name=name, arguments=parsed, raw_arguments=raw)


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Role
    content: str | list[ContentBlock] | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ToolDef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: Message
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    model: str = ""
    raw: dict[str, Any] | None = None


class ToolCallFragment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str = ""


class StreamDelta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    tool_call_fragment: ToolCallFragment | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    final_response: ModelResponse | None = None

