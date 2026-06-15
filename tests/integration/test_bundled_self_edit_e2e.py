from agentkit.control.config import AgentConfig
from agentkit.stores.skill.loader import build_catalog
from agentkit.tools.config import edit_own_config


def test_bundled_skills_load_and_self_edit_applies_on_restart(tmp_path):
    catalog = build_catalog(["agentkit/bundled/skills"])
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  type: mock\n", encoding="utf-8")

    edit_own_config(
        path=str(path),
        new_content="model:\n  type: mock\nbudget:\n  max_tool_calls: 4\n",
    )

    assert {"alfred-agent", "set-goal", "use-memory", "spawn-worker", "create-skill"} <= set(
        catalog.skills
    )
    assert AgentConfig.from_yaml(str(path)).budget.max_tool_calls == 4
