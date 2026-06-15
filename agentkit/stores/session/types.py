from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.providers.types import Message

SessionSource = Literal["cli", "server", "cron", "subagent"]
EndReason = Literal["normal", "compression", "branched", "error"]


class SessionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    source: SessionSource
    model: str = ""
    title: str | None = None
    parent_session_id: str | None = None
    started_at: float
    ended_at: float | None = None
    end_reason: EndReason | None = None
    message_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    message_id: int
    role: str
    timestamp: float
    snippet: str
    context: list[Message] = Field(default_factory=list)
    session_title: str | None = None
    project_id: str

