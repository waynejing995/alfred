from __future__ import annotations

import json

from click.testing import CliRunner

from agentkit.kernel.loop import TurnResult
from agentkit.kernel.providers.types import Message, ToolCall, Usage
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit_cli.main import main
from agentkit_cli.output import render_json, render_stream_json, render_text


def test_text_output_is_final_assistant_message_only() -> None:
    result = _turn_result()

    assert render_text(result) == "final answer"


def test_json_output_is_terminal_object() -> None:
    payload = json.loads(render_json(_turn_result()))

    assert payload == {
        "final_message": "final answer",
        "tool_trace": [
            {
                "id": "call_1",
                "name": "echo",
                "arguments": {"value": "hello"},
                "result": "hello",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "cached_tokens": 0,
            "cache_creation_tokens": 0,
        },
        "stopped": None,
    }


def test_stream_json_output_is_event_frames_plus_final_result() -> None:
    output = render_stream_json(
        _turn_result(),
        events=[{"type": "turn_start", "payload": {"turn_id": "t1"}}],
    )
    frames = [json.loads(line) for line in output.splitlines()]

    assert frames[0] == {"type": "turn_start", "payload": {"turn_id": "t1"}}
    assert frames[1]["type"] == "result"
    assert frames[1]["payload"]["final_message"] == "final answer"


def test_cli_text_output_uses_agent_facade() -> None:
    result = CliRunner().invoke(main, ["chat", "hello"])

    assert result.exit_code == 0
    assert result.output == "mock: hello\n"


def test_cli_json_output_is_valid_json() -> None:
    result = CliRunner().invoke(main, ["chat", "hello", "--output-format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["final_message"] == "mock: hello"
    assert payload["usage"]["total_tokens"] > 0


def test_cli_accepts_config_file(tmp_path) -> None:
    config = tmp_path / "agent.yaml"
    config.write_text("model:\n  type: mock\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["chat", "hello", "--config", str(config)])

    assert result.exit_code == 0
    assert result.output == "mock: hello\n"


def test_cli_stream_json_output_is_valid_jsonl() -> None:
    result = CliRunner().invoke(main, ["chat", "hello", "--output-format", "stream-json"])

    assert result.exit_code == 0
    frames = [json.loads(line) for line in result.output.splitlines()]
    assert [frame["type"] for frame in frames] == [
        "session_start",
        "turn_start",
        "stream_delta",
        "turn_end",
        "result",
    ]
    assert frames[2]["payload"]["text"] == "mock: hello"
    assert frames[-1]["payload"]["final_message"] == "mock: hello"


def test_cli_json_trace_opt_in_returns_trace_id(tmp_path) -> None:
    trace_db = tmp_path / "trace.db"

    result = CliRunner().invoke(
        main,
        [
            "chat",
            "hello",
            "--trace-db",
            str(trace_db),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["final_message"] == "mock: hello"
    assert payload["trace_id"]
    trace = SQLiteTraceStore(trace_db).get_trace(payload["trace_id"])
    assert trace.task == "hello"


def test_cli_stream_json_trace_opt_in_returns_trace_id(tmp_path) -> None:
    trace_db = tmp_path / "trace.db"

    result = CliRunner().invoke(
        main,
        [
            "chat",
            "hello",
            "--trace-db",
            str(trace_db),
            "--output-format",
            "stream-json",
        ],
    )

    assert result.exit_code == 0
    frames = [json.loads(line) for line in result.output.splitlines()]
    assert frames[-1]["type"] == "result"
    assert frames[-1]["payload"]["trace_id"]
    trace = SQLiteTraceStore(trace_db).get_trace(frames[-1]["payload"]["trace_id"])
    assert trace.task == "hello"


def _turn_result() -> TurnResult:
    tool_call = ToolCall(id="call_1", name="echo", arguments={"value": "hello"})
    return TurnResult(
        message=Message(role="assistant", content="final answer"),
        history=[
            Message(role="user", content="prompt"),
            Message(role="assistant", content=None, tool_calls=[tool_call]),
            Message(role="tool", content="hello", tool_call_id="call_1", name="echo"),
            Message(role="assistant", content="final answer"),
        ],
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )
