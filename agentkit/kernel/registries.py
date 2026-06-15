from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.providers.types import ToolDef

ToolHandler = Callable[..., Any | Awaitable[Any]]


class UnknownComponentType(KeyError):
    pass


class RegistryEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    factory: Callable[..., Any]
    params_model: type[BaseModel] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Registry:
    def __init__(self, name: str) -> None:
        self.name = name
        self._entries: dict[str, RegistryEntry] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., Any],
        *,
        params_model: type[BaseModel] | None = None,
        **metadata: Any,
    ) -> None:
        if name in self._entries:
            raise ValueError(f"{self.name} registry entry already exists: {name}")
        self._entries[name] = RegistryEntry(
            name=name,
            factory=factory,
            params_model=params_model,
            metadata=metadata,
        )

    def get(self, name: str) -> RegistryEntry:
        try:
            return self._entries[name]
        except KeyError as exc:
            raise UnknownComponentType(f"unknown {self.name} registry entry: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._entries)


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    permission_bucket: str = "read"
    refundable: bool = False

    def to_tool_def(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=self.parameters)


class ToolsRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: ToolHandler,
        permission_bucket: str,
        refundable: bool = False,
    ) -> None:
        if name in self._entries:
            raise ValueError(f"tool already registered: {name}")
        self._entries[name] = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            permission_bucket=permission_bucket,
            refundable=refundable,
        )

    def get(self, name: str) -> ToolEntry:
        try:
            return self._entries[name]
        except KeyError as exc:
            raise UnknownComponentType(f"unknown tool: {name}") from exc

    def tool_defs(self) -> list[ToolDef]:
        return [entry.to_tool_def() for entry in self._entries.values()]

    def names(self) -> list[str]:
        return sorted(self._entries)


@dataclass
class Registries:
    tools: ToolsRegistry = field(default_factory=ToolsRegistry)
    events: Registry = field(default_factory=lambda: Registry("events"))
    models: Registry = field(default_factory=lambda: Registry("models"))
    skill_sources: Registry = field(default_factory=lambda: Registry("skill_sources"))
    middleware: Registry = field(default_factory=lambda: Registry("middleware"))

