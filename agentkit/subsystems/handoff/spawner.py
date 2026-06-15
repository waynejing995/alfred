from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import Handoff
from agentkit.kernel.loop import TurnCtx, run_turn
from agentkit.kernel.permission import (
    Autonomy,
    Permission,
    PermissionLayer,
    PermissionResolver,
    PermissionRule,
)
from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.registries import ToolEntry, ToolsRegistry
from agentkit.subsystems.handoff.payload import HandoffPayload, HandoffResult


class ToolScopeError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentSpec:
    role: str
    instructions: str = ""
    tool_names: list[str] = field(default_factory=list)
    provider: ModelProvider | None = None


class Spawner:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        tools: ToolsRegistry,
        budget: IterationBudget,
        bus: EventBus | None = None,
        autonomy: Autonomy = Autonomy.ASSIST,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.budget = budget
        self.bus = bus or EventBus()
        self.autonomy = autonomy

    async def spawn(
        self,
        spec: AgentSpec,
        payload: HandoffPayload,
        parent_trace_id: str | None = None,
    ) -> HandoffResult:
        grant = self.budget.reserve(payload.from_agent, n=1)
        if grant is None:
            return HandoffResult(
                agent_id="",
                status="budget_exhausted",
                summary="spawn budget exhausted",
            )
        child_id = f"{spec.role}:{uuid.uuid4().hex[:8]}"
        child_ctx = self._build_isolated_ctx(spec, payload, child_id)
        await self.bus.emit(
            Handoff(
                session_id=child_ctx.session_id,
                payload_ref=f"{parent_trace_id or ''}:{payload.schema_version}:{child_id}",
            )
        )
        turn = await run_turn(child_ctx, payload.objective)
        status = "budget_exhausted" if turn.stopped == "budget" else "ok"
        content = turn.message.content if isinstance(turn.message.content, str) else ""
        return HandoffResult(
            agent_id=child_id,
            status=status,
            summary=content,
            artifacts=payload.artifacts,
            spent=self.budget.spent_by(child_id),
        )

    def _build_isolated_ctx(
        self,
        spec: AgentSpec,
        payload: HandoffPayload,
        child_id: str,
    ) -> TurnCtx:
        scoped_tools = self._scope_tools(spec.tool_names)
        tool_scope = PermissionResolver(
            [
                PermissionLayer(
                    name=f"agent:{child_id}",
                    rules={
                        "tool": [
                            PermissionRule(pattern="*", permission=Permission.DENY),
                            *[
                                PermissionRule(pattern=name, permission=Permission.ALLOW)
                                for name in spec.tool_names
                            ],
                        ]
                    },
                )
            ]
        )
        return TurnCtx(
            provider=spec.provider or self.provider,
            tools=scoped_tools,
            budget=self.budget,
            bus=self.bus,
            history=[],
            permission=PermissionResolver.default(),
            tool_scope=tool_scope,
            autonomy=self.autonomy,
            agent_id=child_id,
        )

    def _scope_tools(self, tool_names: list[str]) -> ToolsRegistry:
        registry = ToolsRegistry()
        for name in tool_names:
            try:
                entry = self.tools.get(name)
            except KeyError as exc:
                raise ToolScopeError(f"unknown scoped tool: {name}") from exc
            _copy_tool(entry, registry)
        return registry


def _copy_tool(entry: ToolEntry, target: ToolsRegistry) -> None:
    target.register(
        name=entry.name,
        description=entry.description,
        parameters=entry.parameters,
        handler=entry.handler,
        permission_bucket=entry.permission_bucket,
        refundable=entry.refundable,
    )

