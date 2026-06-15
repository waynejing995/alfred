import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ALFRED_RUN_REAL_E2E") != "1",
    reason="real LLM e2e runs only under wayne-verify profile",
)


def test_real_anthropic_cli_hashread_tool_call(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("ALFRED_REAL_FILE_CONTENT\n", encoding="utf-8")

    completed = subprocess.run(
        [
            "uv",
            "run",
            "alfred",
            "chat",
            f"Use hashread to read this exact file path and answer with only its content: {target}",
            "--provider",
            "litellm",
            "--model",
            _anthropic_model(),
            "--base-url",
            _anthropic_base_url(),
            "--tool-choice",
            "hashread",
            "--max-tokens",
            "200",
            "--output-format",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "ANTHROPIC_API_KEY": _anthropic_api_key()},
        check=True,
        capture_output=True,
        text=True,
    )
    payload = _json_from_stdout(completed.stdout)

    assert payload["final_message"] == "ALFRED_REAL_FILE_CONTENT"
    assert payload["tool_trace"][0]["name"] == "hashread"
    assert payload["tool_trace"][0]["result"].endswith("|ALFRED_REAL_FILE_CONTENT")


def test_real_anthropic_cli_stream_json_has_deltas():
    completed = subprocess.run(
        [
            "uv",
            "run",
            "alfred",
            "chat",
            "Reply with exactly: ALFRED_STREAM_OK",
            "--provider",
            "litellm",
            "--model",
            _anthropic_model(),
            "--base-url",
            _anthropic_base_url(),
            "--max-tokens",
            "64",
            "--output-format",
            "stream-json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "ANTHROPIC_API_KEY": _anthropic_api_key()},
        check=True,
        capture_output=True,
        text=True,
    )
    frames = [
        _json_from_line(line)
        for line in completed.stdout.splitlines()
        if line.startswith("{")
    ]

    assert any(frame["type"] == "stream_delta" for frame in frames)
    assert frames[-1]["type"] == "result"
    assert "ALFRED_STREAM_OK" in frames[-1]["payload"]["final_message"]


def _anthropic_settings() -> dict:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        pytest.skip("missing ~/.claude/settings.json")
    return json.loads(settings_path.read_text(encoding="utf-8")).get("env", {})


def _anthropic_api_key() -> str:
    key = _anthropic_settings().get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("missing ANTHROPIC_API_KEY")
    return key


def _anthropic_base_url() -> str:
    url = _anthropic_settings().get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    if not url:
        pytest.skip("missing ANTHROPIC_BASE_URL")
    return url


def _anthropic_model() -> str:
    model = (
        os.environ.get("ALFRED_REAL_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or "claude-haiku-4.5"
    )
    return model if "/" in model else f"anthropic/{model}"


def _json_from_stdout(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON object found in stdout: {stdout}")


def _json_from_line(line: str) -> dict:
    return json.loads(line)
