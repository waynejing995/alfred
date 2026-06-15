import pytest

from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.kernel.registries import ToolsRegistry
from agentkit.subsystems.handoff import AgentSpec, HandoffPayload, Spawner, ToolScopeError


def _tools() -> ToolsRegistry:
    registry = ToolsRegistry()
    registry.register(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda text: text,
        permission_bucket="read",
    )
    return registry


async def test_spawner_runs_worker_with_isolated_history_and_handoff_event():
    bus = EventBus()
    events = []
    bus.on("handoff", lambda event: events.append(event))
    provider = MockProvider(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "worker"})],
            ),
            "worker done",
        ]
    )
    spawner = Spawner(provider=provider, tools=_tools(), budget=IterationBudget(5), bus=bus)

    result = await spawner.spawn(
        AgentSpec(role="worker", tool_names=["echo"]),
        HandoffPayload(from_agent="root", objective="do worker task"),
        parent_trace_id="parent-trace",
    )

    assert result.status == "ok"
    assert result.summary == "worker done"
    assert result.agent_id.startswith("worker:")
    assert events
    assert [message.role for message in provider.calls[0]] == ["user"]
    assert provider.calls[0][0].content == "do worker task"


async def test_spawner_rejects_unknown_scoped_tool():
    spawner = Spawner(provider=MockProvider(), tools=_tools(), budget=IterationBudget(5))

    with pytest.raises(ToolScopeError):
        await spawner.spawn(
            AgentSpec(role="worker", tool_names=["missing"]),
            HandoffPayload(from_agent="root", objective="task"),
        )

