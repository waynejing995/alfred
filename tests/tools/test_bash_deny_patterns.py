from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.loop import TurnCtx, dispatch_tool
from agentkit.kernel.permission import Autonomy
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import ToolCall
from agentkit.kernel.registries import ToolsRegistry
from agentkit.tools import register_builtin_tools


async def test_bash_deny_pattern_is_enforced_by_permission_resolver():
    registry = ToolsRegistry()
    register_builtin_tools(registry)
    ctx = TurnCtx(
        provider=MockProvider(),
        tools=registry,
        budget=IterationBudget(1),
        autonomy=Autonomy.AUTO,
    )

    result = await dispatch_tool(
        ctx,
        ToolCall(id="call_1", name="bash", arguments={"command": "rm -rf /tmp/nope"}),
    )

    assert result.ok is False
    assert result.is_error is True
    assert "PermissionDenied" in result.body


async def test_bash_tool_runs_when_permission_allows_under_auto():
    registry = ToolsRegistry()
    register_builtin_tools(registry)
    ctx = TurnCtx(
        provider=MockProvider(),
        tools=registry,
        budget=IterationBudget(1),
        autonomy=Autonomy.AUTO,
    )

    result = await dispatch_tool(
        ctx,
        ToolCall(id="call_1", name="bash", arguments={"command": "printf ok"}),
    )

    assert result.ok is True
    assert '"stdout": "ok"' in result.body
    assert '"returncode": 0' in result.body
