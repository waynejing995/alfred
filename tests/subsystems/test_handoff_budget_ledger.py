from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.kernel.registries import ToolsRegistry
from agentkit.subsystems.handoff import AgentSpec, HandoffPayload, Spawner


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


async def test_spawn_and_worker_ledgers_share_one_budget_pool():
    budget = IterationBudget(3)
    provider = MockProvider(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "worker"})],
            ),
            "done",
        ]
    )
    spawner = Spawner(provider=provider, tools=_tools(), budget=budget)

    result = await spawner.spawn(
        AgentSpec(role="worker", tool_names=["echo"]),
        HandoffPayload(from_agent="root", objective="task"),
    )

    assert result.status == "ok"
    assert budget.spent_by("root") == 1
    assert budget.spent_by(result.agent_id) == 1
    assert budget.reconciles()


async def test_spawn_reports_budget_exhausted_without_running_worker():
    budget = IterationBudget(0)
    provider = MockProvider(["should not run"])
    spawner = Spawner(provider=provider, tools=_tools(), budget=budget)

    result = await spawner.spawn(
        AgentSpec(role="worker", tool_names=["echo"]),
        HandoffPayload(from_agent="root", objective="task"),
    )

    assert result.status == "budget_exhausted"
    assert provider.calls == []

