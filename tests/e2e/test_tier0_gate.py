from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentkit import Agent
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.registries import ToolsRegistry
from agentkit.tools import register_builtin_tools


@pytest.mark.asyncio
async def test_tier0_sdk_reads_file_through_loop_tool_and_events(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    registry = ToolsRegistry()
    register_builtin_tools(registry)
    provider = MockProvider.tool_call(
        name="hashread",
        arguments={"path": str(target)},
        final="I read note.txt.",
    )
    agent = Agent(provider=provider, tools=registry)

    result = await agent.run("Read note.txt")

    assert result.message.content == "I read note.txt."
    assert result.tool_results[0].ok is True
    assert "1:" in result.tool_results[0].body
    assert "|alpha" in result.tool_results[0].body
    assert [event["type"] for event in agent.last_events] == [
        "session_start",
        "turn_start",
        "pre_tool",
        "post_tool",
        "turn_start",
        "turn_end",
    ]


@pytest.mark.asyncio
async def test_tier0_sdk_permission_gate_blocks_dangerous_bash_under_auto():
    registry = ToolsRegistry()
    register_builtin_tools(registry)
    provider = MockProvider.tool_call(
        name="bash",
        arguments={"command": "rm -rf /tmp/alfred-tier0-gate"},
        final="blocked",
    )
    agent = Agent(provider=provider, tools=registry, config={"autonomy": "auto"})

    result = await agent.run("Run a dangerous command")

    assert result.message.content == "blocked"
    assert result.tool_results[0].is_error is True
    assert "PermissionDenied" in result.tool_results[0].body
    assert result.history[-2].role == "tool"
    assert "PermissionDenied" in (result.history[-2].content or "")


@pytest.mark.asyncio
async def test_tier0_sdk_injects_layered_instructions_into_provider_messages(tmp_path):
    alfred_home = tmp_path / "alfred-home"
    alfred_home.mkdir()
    (alfred_home / "AGENTS.md").write_text("GLOBAL_TIER0_RULE", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("PROJECT_TIER0_RULE", encoding="utf-8")
    provider = MockProvider(["ok"])
    agent = Agent(provider=provider, cwd=repo, alfred_home=alfred_home)

    await agent.run("hello")

    first_call = provider.calls[0]
    system = first_call[0]
    content = "\n".join(block.text for block in system.content)
    assert system.role == "system"
    assert "GLOBAL_TIER0_RULE" in content
    assert "PROJECT_TIER0_RULE" in content
    assert system.content[-1].cache_control == {"type": "ephemeral"}
    assert agent.last_events[0]["type"] == "session_start"
    assert agent.last_instruction_manifest == [
        {
            "path": str(alfred_home / "AGENTS.md"),
            "char_count": len("GLOBAL_TIER0_RULE"),
            "included": True,
            "skipped": None,
        },
        {
            "path": str(repo / "AGENTS.md"),
            "char_count": len("PROJECT_TIER0_RULE"),
            "included": True,
            "skipped": None,
        },
    ]


@pytest.mark.asyncio
async def test_tier0_real_model_config_smoke(monkeypatch):
    config = _real_model_config_or_skip(monkeypatch)
    agent = Agent(config=config)

    result = await agent.run("Reply with only this exact token: ALFRED_TIER0_OK")

    assert "ALFRED_TIER0_OK" in (result.message.content or "")
    assert result.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_tier0_cache_prefix_is_stable_across_turns(tmp_path):
    alfred_home = tmp_path / "alfred-home"
    repo = tmp_path / "repo"
    alfred_home.mkdir()
    repo.mkdir()
    (repo / ".git").mkdir()
    (alfred_home / "AGENTS.md").write_text("CACHE_SENTINEL " * 1400, encoding="utf-8")
    provider = MockProvider(["one", "two"])
    agent = Agent(provider=provider, cwd=repo, alfred_home=alfred_home)

    await agent.run("first")
    await agent.run("second")

    first_system = provider.calls[0][0].model_dump(mode="json")
    second_system = provider.calls[1][0].model_dump(mode="json")
    assert first_system == second_system
    assert provider.calls[1][0].content[-1].cache_control == {"type": "ephemeral"}
    assert agent._assembler.prefix.token_floor_estimate() >= 1024


@pytest.mark.asyncio
async def test_tier0_real_model_cache_hit(monkeypatch, tmp_path):
    config = _real_model_config_or_skip(monkeypatch, max_tokens=8)
    alfred_home = tmp_path / "alfred-home"
    repo = tmp_path / "repo"
    alfred_home.mkdir()
    repo.mkdir()
    (repo / ".git").mkdir()
    (alfred_home / "AGENTS.md").write_text("CACHE_SENTINEL " * 1400, encoding="utf-8")
    agent = Agent(config=config, cwd=repo, alfred_home=alfred_home)

    first = await agent.run("Reply exactly: ONE")
    followups = [
        await agent.run("Reply exactly: TWO"),
        await agent.run("Reply exactly: THREE"),
        await agent.run("Reply exactly: FOUR"),
    ]

    assert first.usage.cache_creation_tokens > 0 or first.usage.cached_tokens > 0
    assert any(turn.usage.cached_tokens > 0 for turn in followups)


def test_tier0_cli_stream_json_entrypoint_is_replayable():
    repo = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [
            "uv",
            "run",
            "alfred",
            "chat",
            "tier0 gate",
            "--output-format",
            "stream-json",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    frames = [json.loads(line) for line in completed.stdout.splitlines()]

    assert [frame["type"] for frame in frames] == [
        "session_start",
        "turn_start",
        "turn_end",
        "result",
    ]
    assert frames[-1]["payload"]["final_message"] == "mock: tier0 gate"
    assert frames[-1]["payload"]["usage"]["total_tokens"] > 0


def _real_model_config_or_skip(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_tokens: int = 16,
) -> dict:
    settings_env = _claude_settings_env()
    if settings_env.get("ANTHROPIC_API_KEY"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", settings_env["ANTHROPIC_API_KEY"])
    model = (
        os.environ.get("ALFRED_REAL_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
    )
    if not model:
        pytest.skip("set ALFRED_REAL_MODEL or an ANTHROPIC_DEFAULT_*_MODEL for real-model e2e")
    if "/" not in model:
        model = f"anthropic/{model}"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("set ANTHROPIC_API_KEY for real-model e2e")
    base_url = settings_env.get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    return {
        "model": {
            "type": "litellm",
            "params": {
                "model": model,
                "env_key": "ANTHROPIC_API_KEY",
                "base_url": base_url,
                "extra": {"max_tokens": max_tokens, "temperature": 0},
            },
        }
    }


def _claude_settings_env() -> dict[str, str]:
    path = Path.home() / ".claude" / "settings.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    env = data.get("env", {})
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items() if value}
