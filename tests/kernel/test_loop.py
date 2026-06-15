from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.loop import TurnCtx, run_turn
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.kernel.registries import ToolsRegistry


async def test_loop_dispatches_tool_and_returns_final_answer():
    tools = ToolsRegistry()
    tools.register(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda text: text,
        permission_bucket="read",
    )
    provider = MockProvider(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
            ),
            "done",
        ]
    )

    result = await run_turn(
        TurnCtx(provider=provider, tools=tools, budget=IterationBudget(3), bus=EventBus()),
        "hello",
    )

    assert result.message.content == "done"
    assert result.tool_results[0].body == "hi"


async def test_loop_stops_cleanly_when_budget_exhausted():
    tools = ToolsRegistry()
    tools.register(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda text: text,
        permission_bucket="read",
    )
    provider = MockProvider(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
            )
        ]
    )

    result = await run_turn(
        TurnCtx(provider=provider, tools=tools, budget=IterationBudget(0), bus=EventBus()),
        "hello",
    )

    assert result.stopped == "budget"
    assert result.tool_results[0].is_error is True

