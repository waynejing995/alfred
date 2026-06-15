import pytest
from pydantic import ValidationError

from agentkit.control.autonomy import SelfEditForbidden
from agentkit.control.config import AgentConfig
from agentkit.tools.config import edit_own_config


def test_edit_own_config_applies_after_reload(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  type: mock\n", encoding="utf-8")

    edit_own_config(
        path=str(path),
        new_content="model:\n  type: mock\nbudget:\n  max_tool_calls: 3\n",
    )

    assert AgentConfig.from_yaml(str(path)).budget.max_tool_calls == 3


def test_edit_own_config_validates_new_config(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  type: mock\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        edit_own_config(path=str(path), new_content="not_a_valid_field: true\n")


def test_edit_own_config_rejects_paths_outside_root(tmp_path):
    path = tmp_path.parent / "agent.yaml"

    with pytest.raises(ValueError, match="escapes root"):
        edit_own_config(path=str(path), new_content="model:\n  type: mock\n", root=str(tmp_path))


def test_edit_own_config_rejects_autonomy_self_edit(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("model:\n  type: mock\nautonomy: assist\n", encoding="utf-8")

    with pytest.raises(SelfEditForbidden):
        edit_own_config(path=str(path), new_content="model:\n  type: mock\nautonomy: auto\n")
