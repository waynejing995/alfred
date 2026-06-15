from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from agentkit.control.config import AgentConfig
from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.events.base import serialize
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.loop import TurnCtx, TurnResult, emit_budget_event_to_bus, run_turn
from agentkit.kernel.permission import Autonomy, PermissionResolver
from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.registries import ToolEntry, ToolsRegistry


class Agent:
    """Public SDK facade over the Tier-0 kernel loop."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        tools: ToolsRegistry | Iterable[ToolEntry] | Mapping[str, Any] | None = None,
        config: AgentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.config = _coerce_config(config)
        self.provider = provider or MockProvider()
        self.tools = _coerce_tools(tools)
        self.history = []
        self.session_id = str(uuid.uuid4())
        self.last_events: list[dict[str, Any]] = []

    async def run(self, prompt: str) -> TurnResult:
        bus = EventBus()
        captured: list[dict[str, Any]] = []

        def capture(event):
            captured.append(serialize(event))

        bus.on("*", capture)
        budget = IterationBudget(
            self.config.budget.max_tool_calls if self.config is not None else 20,
            on_event=emit_budget_event_to_bus(bus),
        )
        permission = (
            PermissionResolver(self.config.permission)
            if self.config is not None
            else PermissionResolver.default()
        )
        ctx = TurnCtx(
            provider=self.provider,
            tools=self.tools,
            budget=budget,
            bus=bus,
            history=self.history,
            permission=permission,
            autonomy=self.config.autonomy if self.config is not None else Autonomy.ASSIST,
            session_id=self.session_id,
        )
        result = await run_turn(ctx, prompt)
        await asyncio.sleep(0)
        self.history = ctx.history
        self.last_events = captured
        return result

    def run_sync(self, prompt: str) -> TurnResult:
        return asyncio.run(self.run(prompt))


def _coerce_config(config: AgentConfig | Mapping[str, Any] | None) -> AgentConfig | None:
    if config is None or isinstance(config, AgentConfig):
        return config
    data = dict(config)
    data.setdefault("model", {"type": "mock"})
    return AgentConfig.model_validate(data)


def _coerce_tools(
    tools: ToolsRegistry | Iterable[ToolEntry] | Mapping[str, Any] | None,
) -> ToolsRegistry:
    if isinstance(tools, ToolsRegistry):
        return tools
    registry = ToolsRegistry()
    if tools is None:
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
