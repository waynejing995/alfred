from __future__ import annotations

import pytest

from agentkit import Agent
from agentkit.kernel.providers.mock import MockProvider
from agentkit.stores.trace.sqlite import SQLiteTraceStore


@pytest.mark.asyncio
async def test_public_agent_path_records_trace_steps(tmp_path) -> None:
    trace_store = SQLiteTraceStore(
        tmp_path / "trace.db",
        traces_dir=tmp_path / "traces",
        project_id="project-a",
    )
    provider = MockProvider.tool_call(
        name="echo",
        arguments={"value": "from public path"},
        final="done",
    )
    agent = Agent(
        provider=provider,
        tools={"echo": lambda value: value},
        trace_store=trace_store,
    )

    result = await agent.run("call echo")

    assert result.trace_id is not None
    trace = trace_store.get_trace(result.trace_id)
    assert trace.session_id == agent.session_id
    assert trace.task == "call echo"
    assert [step.tool_name for step in trace.steps] == ["echo"]
    assert trace.steps[0].tool_args == {"value": "from public path"}
    assert trace.annotations[0].kind == "success"
