from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Literal

from agentkit.kernel.loop import TurnResult
from agentkit.kernel.providers.types import Message

OutputFormat = Literal["text", "json", "stream-json"]


def render_result(
    result: TurnResult,
    *,
    output_format: OutputFormat = "text",
    events: Iterable[dict[str, Any]] = (),
) -> str:
    if output_format == "text":
        return render_text(result)
    if output_format == "json":
        return render_json(result)
    if output_format == "stream-json":
        return render_stream_json(result, events=events)
    raise ValueError(f"unsupported output format: {output_format}")


def render_text(result: TurnResult) -> str:
    return _message_text(result.message)


def render_json(result: TurnResult) -> str:
    return _dumps(terminal_payload(result))


def render_stream_json(result: TurnResult, *, events: Iterable[dict[str, Any]]) -> str:
    frames = [*events, final_result_frame(result)]
    return "\n".join(render_stream_frame(frame) for frame in frames)


def render_stream_frame(frame: dict[str, Any]) -> str:
    return _dumps(frame)


def terminal_payload(result: TurnResult) -> dict[str, Any]:
    return {
        "final_message": _message_text(result.message),
        "tool_trace": _tool_trace(result),
        "usage": result.usage.model_dump(mode="json"),
        "stopped": result.stopped,
    }


def final_result_frame(result: TurnResult) -> dict[str, Any]:
    return {"type": "result", "payload": terminal_payload(result)}


def _tool_trace(result: TurnResult) -> list[dict[str, Any]]:
    result_by_call_id = {
        message.tool_call_id: message
        for message in result.history
        if message.role == "tool" and message.tool_call_id
    }
    trace: list[dict[str, Any]] = []
    for message in result.history:
        if message.role != "assistant":
            continue
        for call in message.tool_calls:
            tool_message = result_by_call_id.get(call.id)
            trace.append(
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "result": _message_text(tool_message) if tool_message is not None else None,
                }
            )
    return trace


def _message_text(message: Message | None) -> str:
    if message is None or message.content is None:
        return ""
    if isinstance(message.content, str):
        return message.content
    return "".join(block.text for block in message.content)


def _dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
