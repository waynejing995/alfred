from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.providers.types import ContentBlock, Message, ToolDef


class CacheUsage(BaseModel):
    cached_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    prompt_tokens: int = 0


class FrozenPrefix(BaseModel):
    model_config = ConfigDict(frozen=True)

    tools: list[ToolDef] = Field(default_factory=list)
    persona: str = ""
    user: str = ""
    project_instructions: str = ""
    memory: str = ""
    skill_l0: str = ""
    goal: str | None = None
    fingerprint: str = ""

    @classmethod
    def build(
        cls,
        *,
        tools: list[ToolDef] | None = None,
        persona: str = "",
        user: str = "",
        project_instructions: str = "",
        memory: str = "",
        skill_l0: str = "",
        goal: str | None = None,
    ) -> "FrozenPrefix":
        data = {
            "tools": [tool.model_dump(mode="json") for tool in tools or []],
            "persona": persona,
            "user": user,
            "project_instructions": project_instructions,
            "memory": memory,
            "skill_l0": skill_l0,
            "goal": goal,
        }
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return cls(fingerprint=fingerprint, **data)

    def content_blocks(self) -> list[ContentBlock]:
        segments = [
            ("persona", self.persona),
            ("user", self.user),
            ("project_instructions", self.project_instructions),
            ("memory", self.memory),
            ("skill_l0", self.skill_l0),
            ("goal", self.goal or ""),
        ]
        blocks = [
            ContentBlock(text=f"<{name}>\n{text}\n</{name}>")
            for name, text in segments
            if text
        ]
        if not blocks:
            blocks = [ContentBlock(text="You are Alfred.")]
        blocks[-1] = ContentBlock(
            text=blocks[-1].text,
            cache_control={"type": "ephemeral"},
        )
        return blocks

    def token_floor_estimate(self) -> int:
        text = "\n".join(block.text for block in self.content_blocks())
        return len(text.split())


class AssembledPrompt(BaseModel):
    messages: list[Message]
    prefix_fingerprint: str
    breakpoint_index: int


class ContextAssembler:
    def __init__(self, prefix: FrozenPrefix, *, rolling_breakpoints: int = 0) -> None:
        self.prefix = prefix
        self.rolling_breakpoints = rolling_breakpoints
        self.prefix_dirty = False
        self.epoch = 0

    def assemble(self, tail: list[Message]) -> AssembledPrompt:
        live_fingerprint = FrozenPrefix.build(
            tools=self.prefix.tools,
            persona=self.prefix.persona,
            user=self.prefix.user,
            project_instructions=self.prefix.project_instructions,
            memory=self.prefix.memory,
            skill_l0=self.prefix.skill_l0,
            goal=self.prefix.goal,
        ).fingerprint
        if live_fingerprint != self.prefix.fingerprint:
            raise RuntimeError("frozen prefix fingerprint drifted within an epoch")
        system = Message(role="system", content=self.prefix.content_blocks())
        return AssembledPrompt(
            messages=[system, *tail],
            prefix_fingerprint=self.prefix.fingerprint,
            breakpoint_index=0,
        )

    def compress(self, tail: list[Message], *, protect_last_n: int = 20) -> list[Message]:
        if len(tail) <= protect_last_n:
            return tail
        protected = tail[-protect_last_n:]
        middle = tail[:-protect_last_n]
        summary = Message(
            role="system",
            content=f"Compressed {len(middle)} older messages. Details are unavailable in Tier-0.",
        )
        return [summary, *protected]

    def mark_dirty(self) -> None:
        self.prefix_dirty = True

    def roll_epoch_if_dirty(self, **prefix_kwargs: Any) -> bool:
        if not self.prefix_dirty:
            return False
        self.prefix = FrozenPrefix.build(**prefix_kwargs)
        self.epoch += 1
        self.prefix_dirty = False
        return True

