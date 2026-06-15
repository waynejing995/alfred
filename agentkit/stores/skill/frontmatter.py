from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class SkillFrontmatter(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _NAME_RE.match(value) or "--" in value:
            raise ValueError("name must be kebab-case, 1-64 chars, no leading/trailing dash")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value or len(value) > 1024 or "<" in value or ">" in value:
            raise ValueError("description must be 1-1024 chars and contain no angle brackets")
        return value

    def tags(self) -> set[str]:
        raw = self.metadata.get("tags", "")
        return {part.strip().lower() for part in re.split(r"[\s,]+", raw) if part.strip()}

    def allowed_tool_names(self) -> list[str]:
        names = []
        for token in (self.allowed_tools or "").split():
            name = token.split("(", 1)[0].strip()
            if name:
                names.append(name)
        return names

