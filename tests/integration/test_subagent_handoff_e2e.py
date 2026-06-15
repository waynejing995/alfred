from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.kernel.registries import ToolsRegistry
from agentkit.subsystems.handoff import AgentSpec, HandoffPayload, Spawner, ToolScopeError


def test_subagent_handoff_e2e_tool_scope_and_budget_reconcile():
    async def run():
        registry = ToolsRegistry()
        registry.register(
            name="echo",
            description="Echo input",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=lambda text: text,
            permission_bucket="read",
        )
        budget = IterationBudget(4)
        provider = MockProvider(
            [
                Message(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(id="call_1", name="echo", arguments={"text": "worker"})
                    ],
                ),
                "worker done",
            ]
        )
        spawner = Spawner(provider=provider, tools=registry, budget=budget)
        result = await spawner.spawn(
            AgentSpec(role="worker", tool_names=["echo"]),
            HandoffPayload(from_agent="root", objective="task"),
        )
        try:
            await spawner.spawn(
                AgentSpec(role="worker", tool_names=["bash"]),
                HandoffPayload(from_agent="root", objective="bad"),
            )
        except ToolScopeError:
            scoped = True
        else:
            scoped = False
        return result, scoped, budget

    import asyncio

    result, scoped, budget = asyncio.run(run())

    assert result.status == "ok"
    assert scoped is True
    assert budget.reconciles()

