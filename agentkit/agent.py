from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agentkit.control.config import AgentConfig
from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.context import ContextAssembler, FrozenPrefix
from agentkit.kernel.events.base import serialize
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import SessionStart
from agentkit.kernel.instructions import InstructionResolver
from agentkit.kernel.loop import TurnCtx, TurnResult, emit_budget_event_to_bus, run_turn
from agentkit.kernel.permission import Autonomy, PermissionResolver
from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.config import LiteLLMParams, build_litellm_provider
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.registries import ToolEntry, ToolsRegistry
from agentkit.tools import register_builtin_tools


class Agent:
    """Public SDK facade over the Tier-0 kernel loop."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        tools: ToolsRegistry | Iterable[ToolEntry] | Mapping[str, Any] | None = None,
        config: AgentConfig | Mapping[str, Any] | None = None,
        cwd: str | Path | None = None,
        alfred_home: str | Path | None = None,
    ) -> None:
        self.config = _coerce_config(config)
        self.provider = provider or _provider_from_config(self.config)
        self.tools = _coerce_tools(tools)
        self.cwd = Path(cwd or ".").resolve()
        self.alfred_home = Path(alfred_home).expanduser() if alfred_home is not None else None
        self.history: list = []
        self.session_id = str(uuid.uuid4())
        self.last_events: list[dict[str, Any]] = []
        self.last_instruction_manifest: list[dict[str, object]] = []
        self._assembler: ContextAssembler | None = None

    async def run(self, prompt: str, *, stream: bool = False, event_sink=None) -> TurnResult:
        bus = EventBus()
        captured: list[dict[str, Any]] = []

        def capture(event):
            frame = serialize(event)
            captured.append(frame)
            if event_sink is not None:
                event_sink(frame)

        bus.on("*", capture)
        new_session = self._assembler is None
        if self._assembler is None:
            self._assembler = self._build_assembler()
        if new_session:
            await bus.emit(
                SessionStart(
                    session_id=self.session_id,
                    epoch=self._assembler.epoch,
                    manifest={"instructions": self.last_instruction_manifest},
                )
            )
        budget = IterationBudget(
            self.config.budget.max_tool_calls if self.config is not None else 20,
            on_event=emit_budget_event_to_bus(bus),
        )
        permission = _permission_from_config(self.config)
        ctx = TurnCtx(
            provider=self.provider,
            tools=self.tools,
            budget=budget,
            bus=bus,
            history=self.history,
            assembler=self._assembler,
            permission=permission,
            autonomy=self.config.autonomy if self.config is not None else Autonomy.ASSIST,
            session_id=self.session_id,
        )
        result = await run_turn(ctx, prompt, stream=stream)
        await asyncio.sleep(0)
        self.history = ctx.history
        self.last_events = captured
        return result

    def run_sync(self, prompt: str, *, stream: bool = False, event_sink=None) -> TurnResult:
        return asyncio.run(self.run(prompt, stream=stream, event_sink=event_sink))

    def _build_assembler(self) -> ContextAssembler:
        resolved = InstructionResolver().resolve(self.cwd, self.alfred_home)
        self.last_instruction_manifest = resolved.manifest()
        return ContextAssembler(
            FrozenPrefix.build(
                tools=self.tools.tool_defs(),
                project_instructions=resolved.merged,
            )
        )


def _coerce_config(config: AgentConfig | Mapping[str, Any] | None) -> AgentConfig | None:
    if config is None or isinstance(config, AgentConfig):
        return config
    data = dict(config)
    data.setdefault("model", {"type": "mock"})
    return AgentConfig.model_validate(data)


def _provider_from_config(config: AgentConfig | None) -> ModelProvider:
    if config is None or config.model.type == "mock":
        return MockProvider()
    if config.model.type == "litellm":
        return build_litellm_provider(LiteLLMParams.model_validate(config.model.params))
    raise ValueError(f"unsupported model provider type: {config.model.type}")


def _permission_from_config(config: AgentConfig | None) -> PermissionResolver:
    resolver = PermissionResolver.default()
    if config is None:
        return resolver
    for layer in config.permission:
        resolver = resolver.with_layer(layer)
    return resolver


def _coerce_tools(
    tools: ToolsRegistry | Iterable[ToolEntry] | Mapping[str, Any] | None,
) -> ToolsRegistry:
    if isinstance(tools, ToolsRegistry):
        return tools
    registry = ToolsRegistry()
    if tools is None:
        register_builtin_tools(registry)
        return registry
    if isinstance(tools, Mapping):
        for name, handler in tools.items():
            registry.register(
                name=name,
                description=f"User supplied tool: {name}",
                parameters={"type": "object", "properties": {}},
                handler=handler,
                permission_bucket="read",
            )
        return registry
    for entry in tools:
        if not isinstance(entry, ToolEntry):
            raise TypeError("tools iterable must contain ToolEntry instances")
        registry.register(
            name=entry.name,
            description=entry.description,
            parameters=entry.parameters,
            handler=entry.handler,
            permission_bucket=entry.permission_bucket,
            refundable=entry.refundable,
        )
    return registry


__all__ = ["Agent"]
