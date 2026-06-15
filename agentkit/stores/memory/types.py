from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryBlockKind = Literal["persona", "user", "fact"]
MemoryWriteOp = Literal["append", "replace"]


class MemoryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    persona: str = ""
    user: str = ""
    goal: str | None = None
    resumed_tail: list[str] = Field(default_factory=list)

    def seed_query(self) -> str:
        parts = [self.user, self.persona, self.goal or "", *self.resumed_tail]
        return "\n".join(part for part in parts if part)


class MemoryBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: MemoryBlockKind
    text: str
    source: str
    score: float = 1.0
    summary: str = ""
    entities: list[str] = Field(default_factory=list)
    project_id: str | None = None


class RetrievedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[MemoryBlock] = Field(default_factory=list)
    query: str = ""
    token_estimate: int = 0


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: MemoryWriteOp
    text: str
    target: str | None = None
    summary: str = ""
    entities: list[str] = Field(default_factory=list)
    source_session: str | None = None
