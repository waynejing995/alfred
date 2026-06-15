from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file", "trace", "memory", "session"]
    ref: str
    summary: str = ""


class HandoffPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    from_agent: str
    control: Literal["returnable", "one_way"] = "returnable"
    objective: str
    output_format: str = ""
    context_refs: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class HandoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    status: Literal["ok", "error", "budget_exhausted", "incomplete"]
    summary: str
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    spent: int = 0

