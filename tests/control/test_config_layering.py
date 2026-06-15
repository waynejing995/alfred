import pytest

from agentkit.control.config import AgentConfig, ComponentSpec, ConfigError, deep_merge


def test_deep_merge_replaces_node_when_type_changes():
    base = {"model": {"type": "mock", "params": {"a": 1}}}
    override = {"model": {"type": "litellm", "params": {"model": "x"}}}

    assert deep_merge(base, override) == override


def test_from_yaml_layers_and_env_override(tmp_path, monkeypatch):
    base = tmp_path / "base.yaml"
    project = tmp_path / "project.yaml"
    base.write_text(
        "model:\n  type: mock\n  params:\n    model: base\nbudget:\n  max_tool_calls: 2\n",
        encoding="utf-8",
    )
    project.write_text("budget:\n  max_tool_calls: 5\n", encoding="utf-8")
    monkeypatch.setenv("ALFRED_MODEL__TYPE", "litellm")
    monkeypatch.setenv("ALFRED_MODEL__PARAMS__MODEL", "anthropic/claude")
    monkeypatch.setenv("ALFRED_MODEL__PARAMS__ENV_KEY", "ANTHROPIC_API_KEY")

    config = AgentConfig.from_yaml(str(base), str(project))

    assert config.model == ComponentSpec(
        type="litellm",
        params={"model": "anthropic/claude", "env_key": "ANTHROPIC_API_KEY"},
    )
    assert config.budget.max_tool_calls == 5


def test_env_interpolation_in_yaml(tmp_path, monkeypatch):
    path = tmp_path / "agent.yaml"
    path.write_text(
        "model:\n  type: litellm\n  params:\n    model: ${MODEL_ID}\n    env_key: KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_ID", "anthropic/claude")

    assert AgentConfig.from_yaml(str(path)).model.params["model"] == "anthropic/claude"


def test_plaintext_api_key_is_rejected_in_yaml(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(
        "model:\n  type: litellm\n  params:\n    model: x\n    api_key: secret\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        AgentConfig.from_yaml(str(path))


def test_plaintext_api_key_is_rejected_in_direct_spec():
    with pytest.raises(ConfigError):
        ComponentSpec(type="litellm", params={"api_key": "secret"})

