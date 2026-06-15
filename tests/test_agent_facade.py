from __future__ import annotations

import pytest

from agentkit import Agent
from agentkit.kernel.loop import TurnResult
from agentkit.kernel.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_agent_run_uses_mock_provider_by_default() -> None:
    agent = Agent()

    result = await agent.run("hello")

    assert isinstance(result, TurnResult)
    assert result.message.content == "mock: hello"
    assert result.usage.total_tokens > 0
    assert [event["type"] for event in agent.last_events] == [
        "session_start",
        "turn_start",
        "turn_end",
    ]


def test_agent_run_sync_returns_turn_result() -> None:
    result = Agent().run_sync("smoke")

    assert isinstance(result, TurnResult)
    assert result.message.content == "mock: smoke"


@pytest.mark.asyncio
async def test_agent_dispatches_supplied_tools() -> None:
    provider = MockProvider.tool_call(name="echo", arguments={"value": "from tool"}, final="done")
    agent = Agent(provider=provider, tools={"echo": lambda value: value})

    result = await agent.run("call echo")

    assert result.message.content == "done"
    assert len(result.tool_results) == 1
    assert result.tool_results[0].body == "from tool"
    assert [event["type"] for event in agent.last_events] == [
        "session_start",
        "turn_start",
        "pre_tool",
        "post_tool",
        "turn_start",
        "turn_end",
    ]


@pytest.mark.asyncio
async def test_agent_accepts_partial_tier0_config() -> None:
    provider = MockProvider.tool_call(name="echo", arguments={"value": "blocked"}, final="unused")
    agent = Agent(
        provider=provider,
        tools={"echo": lambda value: value},
        config={"budget": {"max_tool_calls": 0}},
    )

    result = await agent.run("call echo")

    assert result.stopped == "budget"
    assert result.tool_results[0].body == "iteration budget exhausted"


@pytest.mark.asyncio
async def test_agent_passes_tool_choice_to_provider() -> None:
    provider = MockProvider(["done"])
    agent = Agent(provider=provider)

    await agent.run("use a tool", tool_choice="hashread")

    assert provider.calls
    assert provider.tool_choices == ["hashread"]
