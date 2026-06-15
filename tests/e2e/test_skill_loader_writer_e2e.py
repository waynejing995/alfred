import pytest

from agentkit import Agent
from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.loop import TurnCtx, dispatch_tool
from agentkit.kernel.permission import Autonomy, PermissionDenied, PermissionResolver
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import ToolCall
from agentkit.kernel.registries import ToolsRegistry
from agentkit.stores.skill.loader import build_catalog
from agentkit.stores.skill.permissions import permission_layer_for_skill
from agentkit.stores.skill.writer import SkillStoreWriter
from agentkit.tools import register_builtin_tools


def write_skill(root, name, body, *, allowed_tools=None):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    allowed = f"allowed-tools: {' '.join(allowed_tools)}\n" if allowed_tools else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use this skill.\n{allowed}---\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.mark.asyncio
async def test_skill_loader_writer_e2e_l0_prefix_and_allowed_tools_scope(tmp_path):
    high = tmp_path / "high"
    low = tmp_path / "low"
    write_skill(high, "demo-skill", "high", allowed_tools=["hashread"])
    write_skill(low, "demo-skill", "low")
    writer = SkillStoreWriter(high)
    await writer.write_skill(
        name="writer-skill",
        description="Use writer skill.",
        body="generated",
        origin="distill",
    )
    catalog = build_catalog([high, low])
    model = MockProvider(["ok"])
    agent = Agent(provider=model, skill_catalog=catalog)

    await agent.run("hello")

    system_text = "\n".join(block.text for block in model.calls[0][0].content)
    assert "- demo-skill: Use this skill." in system_text
    assert "- writer-skill: Use writer skill." in system_text
    assert catalog.shadowed[0][0] == "demo-skill"
    layer = permission_layer_for_skill(
        catalog.get("demo-skill"),
        known_tool_names=["hashread", "bash"],
    )
    resolver = PermissionResolver.default().with_layer(layer)
    resolver.assert_allowed(
        tool_name="hashread",
        bucket="tool",
        action="hashread",
        autonomy=Autonomy.AUTO,
    )
    with pytest.raises(PermissionDenied):
        resolver.assert_allowed(
            tool_name="bash",
            bucket="tool",
            action="bash",
            autonomy=Autonomy.AUTO,
        )

    tools = ToolsRegistry()
    register_builtin_tools(tools)
    result = await dispatch_tool(
        TurnCtx(
            provider=MockProvider(),
            tools=tools,
            budget=IterationBudget(1),
            autonomy=Autonomy.AUTO,
            tool_scope=resolver,
        ),
        ToolCall(id="call_1", name="bash", arguments={"command": "printf bad"}),
    )

    assert result.is_error is True
    assert "PermissionDenied" in result.body

    skill_md = high / "writer-skill" / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: writer-skill\n"
        "description: Use writer skill.\n"
        "metadata:\n"
        "  origin: human\n"
        "---\n"
        "manual\n",
        encoding="utf-8",
    )
    edited = build_catalog([high]).get("writer-skill")

    assert edited.body == "manual"
    assert edited.frontmatter.metadata["origin"] == "human"
